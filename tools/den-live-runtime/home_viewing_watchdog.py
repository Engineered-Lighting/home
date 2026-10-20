"""Attach-only supervision of one Home-created viewing session; never Start.

Run as ``python -m tools.den-live-runtime.home_viewing_watchdog`` from the
checkout root (the directory contains an unrelated operator.py). Default is
inert preparation. Execution requires a fresh owner-ready window. Arm BEFORE
Home Start, then publish the exact Home analysisState using write_binding().
The 90-second deadline never moves when binding arrives late. Stop has its own
thread and API client; evidence/fsync/browser work cannot hold its lane.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import sys
import threading
import time
import uuid
import urllib.error

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
SHA = re.compile(r'[a-f0-9]{64}')
TERMINAL = {'stopped', 'failed', 'interrupted', 'blocked'}
CAP = 32 * 1024**2
MEDIA_CHUNK=65536
MAX_IMAGE=128*MEDIA_CHUNK
# Announced metric PNGs (live apartment_viewing.metric.registered_frame) are a
# narrow sub-lane of the same image cap: at most 6 files and 6 MiB in total.
METRIC_PNG_MAX=6
METRIC_PNG_BYTES=6*1024**2
REGISTERED_FRAME_KEYS={'media_id','frame_id','media_sha256','generation','event_id'}


class MediaBudget:
    def __init__(self):self.bytes=0;self.requests=0
    def request(self,route):
        if self.requests>=1024:raise SampleUnavailable(dict(status=None,route=route,error_code='media_request_budget'),permanent=True)
        self.requests+=1
    def receive(self,count):
        if self.bytes+count>CAP:raise ValueError('media_transfer_budget_exhausted')
        self.bytes+=count


class MediaFailure(ValueError):
    def __init__(self, reason, diagnostic):
        super().__init__(reason);self.diagnostic=diagnostic


class SampleUnavailable(Exception):
    def __init__(self, diagnostic, *, permanent=False):
        super().__init__('sample_unavailable');self.diagnostic=diagnostic;self.permanent=permanent


def http_diagnostic(error, route):
    raw=error.read(2049)
    try:body=json.loads(raw[:2048]);code=body.get('error',{}).get('code')
    except (ValueError,TypeError,AttributeError):code=None
    error.close()
    return dict(status=error.code,route=route,error_code=code if isinstance(code,str) and re.fullmatch('[a-z0-9_]{1,128}',code) else 'unavailable',
        body_bytes=min(len(raw),2048),body_sha256=hashlib.sha256(raw[:2048]).hexdigest(),body_truncated=len(raw)>2048)


def read_sampled_image(api, whole, watchdog, owner, frame, monotonic, *, limit=None):
    mid=frame['media_id'];sid=owner['session_id'];generation=owner['generation']
    route=f'/sessions/{sid}/media/{mid}/descriptor?generation={generation}'
    budget=getattr(watchdog,'media_budget',None)
    if budget is None:budget=watchdog.media_budget=MediaBudget()
    def fence():
        if monotonic()>=watchdog.deadline:raise SampleUnavailable(dict(status=None,route=route,error_code='acquisition_deadline'))
    try:
        owner=binding(owner);fid=frame.get('id',frame.get('frame_id'))
        if (owner['session_id'] is None or not isinstance(mid,str) or not ID.fullmatch(mid) or not mid.startswith('view_')
                or frame.get('site_id')!=owner['site_id'] or type(frame.get('generation')) is not int
                or frame['generation']!=generation or not isinstance(fid,str) or not 0<len(fid)<=256):
            raise ValueError('sample_frame_scope_mismatch')
        fence();budget.request(route)
        descriptor=api('GET',route)
        if not isinstance(descriptor,dict):raise ValueError('bounded_sample_descriptor_required')
        size=descriptor.get('size_bytes',descriptor.get('size'))
        if type(size) is not int or not 0<size<=MAX_IMAGE:raise ValueError('bounded_sample_descriptor_required')
        meta=descriptor.get('metadata');digest=descriptor.get('sha256');mime_type=descriptor.get('mime_type')
        if (any(descriptor.get(k)!=v for k,v in (('session_id',sid),('site_id',owner['site_id']),('generation',generation)))
                or type(descriptor.get('generation')) is not int or (descriptor.get('id') or descriptor.get('media_id'))!=mid
                or any(k in descriptor and descriptor[k]!=mid for k in ('id','media_id'))
                or any(k in descriptor and (type(descriptor[k]) is not int or descriptor[k]!=size) for k in ('size','size_bytes'))
                or not isinstance(digest,str) or not SHA.fullmatch(digest) or mime_type not in {'image/jpeg','image/png'}
                or descriptor.get('content_type',mime_type)!=mime_type or not isinstance(meta,dict)
                or meta.get('site_id')!=owner['site_id'] or meta.get('camera_id')!=owner['camera_id'] or meta.get('frame_id')!=fid
                or not isinstance(meta.get('capture_epoch'),str) or not 0<len(meta['capture_epoch'])<=256
                or 'capture_epoch' in frame and meta['capture_epoch']!=frame['capture_epoch']):
            raise ValueError('sample_descriptor_scope_or_integrity_mismatch')
        if size>CAP-budget.bytes:raise SampleUnavailable(dict(status=None,route=route,error_code='media_transfer_budget'),permanent=True)
        if limit is not None and size>limit:raise SampleUnavailable(dict(status=None,route=route,error_code='metric_png_budget'))
        fence()
        route=f'/sessions/{sid}/media/{mid}?generation={generation}'
        chunks=[];content_type=None
        for start in range(0,size,MEDIA_CHUNK):
            fence();budget.request(route);end=min(size-1,start+MEDIA_CHUNK-1);length=end-start+1
            request=whole.urllib.request.Request(whole.ORIGIN+whole.PREFIX+route,
                headers={'Authorization':'Bearer '+api.token,'Accept-Encoding':'identity','Range':f'bytes={start}-{end}'})
            with api.opener.open(request,timeout=min(5,max(.001,watchdog.deadline-monotonic()))) as response:
                fence()
                if (response.status!=206 or response.getheader('Content-Encoding','identity')!='identity'
                        or response.getheader('Content-Range')!=f'bytes {start}-{end}/{size}'
                        or response.getheader('Content-Length')!=str(length) or response.getheader('ETag')!='"'+digest+'"'):
                    raise ValueError('exact_bounded_image_range_required')
                mime=response.getheader('Content-Type','').split(';')[0]
                if mime!=mime_type or content_type is not None and content_type!=mime:raise ValueError('image_mime_changed')
                raw=response.read(length);budget.receive(len(raw));fence()
                if len(raw)!=length:raise ValueError('exact_bounded_image_range_required')
                content_type=mime;chunks.append(raw)
        raw=b''.join(chunks)
        if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('sample_image_sha256_mismatch')
        return dict(bytes=raw,descriptor=descriptor,type=content_type)
    except urllib.error.HTTPError as error:
        diagnostic=http_diagnostic(error,route)
        if diagnostic['status']==410 and diagnostic['error_code']=='media_unavailable':raise SampleUnavailable(diagnostic) from None
        raise MediaFailure('media_http_error',diagnostic) from None
    except ValueError as error:
        raise MediaFailure(str(error),dict(status=None,route=route,error_code=str(error))) from None


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def binding(value):
    keys = {'schema','version','request_id','site_id','camera_id','experiment_id','session_id','generation'}
    if not isinstance(value,dict) or set(value)!=keys:
        raise ValueError('closed_home_binding_required')
    try:
        request = uuid.UUID(value['request_id'])
        secure = request.version == 4 and str(request) == value['request_id']
    except (ValueError,TypeError,AttributeError): secure = False
    sid, generation = value['session_id'], value['generation']
    if (value['schema']!='home-viewing-watchdog-binding-1' or type(value['version']) is not int or value['version']!=1
            or not secure or value['site_id']!='victoria' or value['camera_id']!='den'
            or value['experiment_id']!='apartment-viewing'
            or not ((sid is None and generation is None) or isinstance(sid,str) and ID.fullmatch(sid)
                and type(generation) is int and generation>=0)):
        raise ValueError('exact_home_viewing_binding_required')
    return deepcopy(value)


def read_private_json(path, maximum=4096):
    path=Path(path)
    if not path.is_absolute(): raise ValueError('absolute_private_file_required')
    try: info=path.lstat()
    except FileNotFoundError: return None
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or not 0<info.st_size<=maximum
            or os.name!='nt' and (info.st_uid!=os.geteuid() or info.st_mode&0o077)):
        raise ValueError('bounded_private_regular_file_required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        opened=os.fstat(fd)
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino): raise ValueError('private_file_changed')
        with os.fdopen(fd,'rb',closefd=False) as stream: raw=stream.read(maximum+1)
        if len(raw)>maximum: raise ValueError('private_file_bound')
        return json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite_private_json')))
    finally: os.close(fd)


def write_binding(path, value):
    """Publish a complete private binding atomically without replacing a file."""
    path=Path(path);raw=encoded(binding(value))+b'\n'
    if not path.is_absolute() or not path.parent.is_dir() or len(raw)>4096:
        raise ValueError('fresh_absolute_binding_destination_required')
    temporary=path.with_name(path.name+'.pending-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path)  # Atomic and fails if destination already exists.
    finally: temporary.unlink(missing_ok=True)


class Watchdog:
    def __init__(self, api, binding_loader, cleanup_probe, *, monotonic=time.monotonic,
                 wait=time.sleep, retain=None, evidence_api=None, image_reader=None, max_images=0, metric_pngs=0):
        if (not all(callable(v) for v in (api,binding_loader,cleanup_probe,monotonic,wait))
                or retain is not None and not callable(retain)
                or image_reader is not None and not callable(image_reader)
                or type(max_images) is not int or not 0<=max_images<=90
                or type(metric_pngs) is not int or not 0<=metric_pngs<=min(METRIC_PNG_MAX,max_images)):
            raise ValueError('bounded_watchdog_ports_required')
        self.api,self.load,self.probe=api,binding_loader,cleanup_probe
        self.clock,self.pause,self.retain=monotonic,wait,retain
        self.evidence_api=evidence_api or api;self.image_reader=image_reader;self.max_images=max_images
        self.metric_pngs=metric_pngs;self.metric_png_ids=set();self.metric_png_bytes=0;self.metric_png_attempts=set()
        self.lock=threading.RLock();self.finished=threading.Event();self.abort=threading.Event()
        self.armed=False;self.owner=None;self.supplied=None;self.verified=False;self.cursor=0;self.events={};self.observed_media=set()
        self.image_ids=set();self.image_bytes=0;self.metadata_bytes=0;self.event_bytes=0;self.evidence_error=None;self.errors=[]
        self.sample_attempts=set();self.missed_images=0;self.media_http=[];self.sampling_exhausted=False
        self.media_budget=MediaBudget()
        self.state=dict(schema='home-viewing-watchdog-result-1',version=1,status='prepared',outcome=None,
            binding=None,finished=False,stop_started_at=None,stop_attempts=0,cleanup_confirmed=False,
            operator_seconds=90,cleanup_seconds=30,starts_sessions=False,physical_permission=False,
            ram_status='unconfirmed',heap_residency='unmeasured',complete_image_replay=False)

    def snapshot(self):
        with self.lock:
            return deepcopy(dict(self.state,event_count=len(self.events),event_first=min(self.events,default=None),
                event_last=max(self.events,default=None),images=len(self.image_ids),image_bytes=self.image_bytes,
                metadata_bytes=self.metadata_bytes,evidence_error=self.evidence_error,errors=self.errors,
                missed_images=self.missed_images,media_http=self.media_http,sample_attempts=len(self.sample_attempts),
                image_transfer_bytes=self.media_budget.bytes,media_requests=self.media_budget.requests,
                metric_png_cap=self.metric_pngs,metric_pngs=len(self.metric_png_ids),metric_png_bytes=self.metric_png_bytes,
                metric_png_attempts=len(self.metric_png_attempts),
                image_evidence_complete=self.max_images==0 or bool(self.image_ids)))

    def _error(self, error):
        with self.lock:
            if len(self.errors)<100:self.errors.append((str(error) if type(error) is ValueError else type(error).__name__)[:512])

    def _binding(self):
        value=self.load()
        if value is None:return self.owner
        value=binding(value)
        with self.lock:
            if self.supplied is not None and value!=self.supplied:raise ValueError('home_binding_changed')
            if self.supplied is None:
                self.supplied=value;self.owner=deepcopy(value);self.state['binding']=deepcopy(value)
            return deepcopy(self.owner)

    def _session(self, row, owner):
        if (not isinstance(row,dict) or any(row.get(k)!=owner[k] for k in ('request_id','site_id','camera_id','experiment_id'))
                or not isinstance(row.get('id'),str) or not ID.fullmatch(row['id'])
                or type(row.get('generation')) is not int or row['generation']<0
                or row.get('state') not in {'requested','starting','recording','stopping','indeterminate',*TERMINAL}
                or owner['session_id'] is not None and (row['id']!=owner['session_id'] or row['generation']!=owner['generation'])):
            raise ValueError('owned_session_binding_mismatch')
        return row

    def _call(self, api, method, suffix, body=None):
        if self.armed and (self.clock()>=self.cleanup_deadline or self.finished.is_set()):
            raise ValueError('cleanup_deadline_elapsed')
        with self.lock: owner=deepcopy(self.owner)
        sid=owner and owner['session_id']
        allowed=method=='GET' and suffix in {'/sessions','/capabilities'}
        if sid:
            allowed=allowed or method=='GET' and (suffix==f'/sessions/{sid}/live'
                or re.fullmatch(r'/sessions/'+re.escape(sid)+r'/events\?after_id=\d+&limit=200',suffix)
                or re.fullmatch(r'/sessions/'+re.escape(sid)+r'/media/[A-Za-z0-9_-]+/descriptor\?generation=\d+',suffix))
            allowed=allowed or method=='POST' and suffix==f'/sessions/{sid}/stop' and body=={'request_id':owner['request_id']}
        if not allowed:raise ValueError('attach_only_api_route_required')
        value=api(method,suffix,body)
        if self.armed and (self.clock()>=self.cleanup_deadline or self.finished.is_set()):
            raise ValueError('cleanup_deadline_elapsed')
        return value

    def _resolve(self, api):
        with self.lock: owner=deepcopy(self.owner)
        if owner is None:return None
        if owner['session_id'] is not None:
            with self.lock:
                if self.verified:return owner
            live=self._call(api,'GET',f"/sessions/{owner['session_id']}/live")
            if live.get('site_id')!='victoria' or live.get('session_id')!=owner['session_id']:
                raise ValueError('owned_live_scope_mismatch')
            self._session(live.get('session'),owner)
            with self.lock:self.verified=True;self.state['binding_verified']=True
            return owner
        page=self._call(api,'GET','/sessions')
        if page.get('site_id')!='victoria' or not isinstance(page.get('items'),list) or len(page['items'])>200:
            raise ValueError('bounded_owned_session_list_required')
        matches=[row for row in page['items'] if row.get('request_id')==owner['request_id']]
        if len(matches)!=1:raise ValueError('original_request_acknowledgement_unresolved')
        row=self._session(matches[0],owner)
        with self.lock:
            if self.owner['session_id'] is not None and (self.owner['session_id'],self.owner['generation'])!=(row['id'],row['generation']):
                raise ValueError('owned_session_binding_mismatch')
            self.owner.update(session_id=row['id'],generation=row['generation']);self.state['binding']=deepcopy(self.owner)
            self.verified=True;self.state['binding_verified']=True
            return deepcopy(self.owner)

    def _events(self, api, owner):
        with self.lock: after=self.cursor
        page=self._call(api,'GET',f"/sessions/{owner['session_id']}/events?after_id={after}&limit=200")
        rows=page.get('items');last=after
        if not isinstance(rows,list) or len(rows)>200:raise ValueError('bounded_event_page_required')
        for event in rows:
            if (not isinstance(event,dict) or event.get('session_id')!=owner['session_id']
                    or type(event.get('id')) is not int or event['id']<=last
                    or not isinstance(event.get('kind'),str) or not isinstance(event.get('payload'),dict)):
                raise ValueError('ordered_owned_events_required')
            last=event['id']
        if page.get('after_id')!=last:raise ValueError('owned_event_cursor_mismatch')
        with self.lock:
            for event in rows:
                old=self.events.get(event['id'])
                if old is not None and old!=event:raise ValueError('original_event_changed')
                if old is None:
                    size=len(encoded(event))
                    if len(self.events)>=6000 or self.event_bytes+size>CAP:raise ValueError('event_budget_exhausted')
                    self.event_bytes+=size
                self.events[event['id']]=deepcopy(event)
            self.cursor=max(self.cursor,last)
        return page

    def _cleanup(self, owner):
        live=self._call(self.api,'GET',f"/sessions/{owner['session_id']}/live")
        if live.get('site_id')!='victoria' or live.get('session_id')!=owner['session_id']:
            raise ValueError('owned_live_scope_mismatch')
        row=self._session(live.get('session'),owner)
        if row['state'] not in TERMINAL:return self._refusal('session_not_terminal')
        caps=self._call(self.api,'GET','/capabilities');last=caps.get('last_worker_cleanup',{})
        workers=last.get('workers',{})
        if (caps.get('site_id')!='victoria' or last.get('session_id')!=owner['session_id'] or last.get('state')!='stopped'
                or set(workers)!={'a','b'} or any(v.get('state') not in {'stopped','failed'} for v in workers.values())):
            return self._refusal('worker_release_unconfirmed')
        self._events(self.api,owner)
        with self.lock: events=deepcopy(list(self.events.values()))
        drained={e['payload'].get('request_id') for e in events if e['kind']=='workers.local_drained'}
        cleanup=any(p.get('state')=='stopped' and (p.get('local_drain')=='drained' or p.get('request_id') in drained)
            and set(p.get('workers',{}))=={'a','b'} and all(v.get('state') in {'stopped','failed'} for v in p['workers'].values())
            for p in (e['payload'] for e in events if e['kind']=='workers.cleanup'))
        if not cleanup:return self._refusal('local_drain_unconfirmed')
        proof=self.probe(deepcopy(owner))
        if self.clock()>=self.cleanup_deadline or self.finished.is_set():return False
        if isinstance(proof,dict) and len(encoded(proof))<=32768:
            with self.lock:self.state['reservation_observation']=deepcopy(proof)
        if (not isinstance(proof,dict) or len(encoded(proof))>32768
                or proof.get('schema')!='home-viewing-reservation-release-1' or type(proof.get('version')) is not int or proof['version']!=1
                or type(proof.get('generation')) is not int
                or any(proof.get(k)!=owner[k] for k in ('request_id','session_id','generation'))
                or proof.get('status') not in {'observed_zero','released_by_terminal_receipts'} or proof.get('unresolved') is not False
                or proof.get('release_scopes')!=['owned_jobs','paired','reasoning','remote']
                or not isinstance(proof.get('evidence_sha256'),list) or not 1<=len(proof['evidence_sha256'])<=32
                or any(not isinstance(s,str) or not SHA.fullmatch(s) for s in proof['evidence_sha256'])
                or not isinstance(proof.get('source_contract_sha256'),str) or not SHA.fullmatch(proof['source_contract_sha256'])):
            return self._refusal('reservation_release_unconfirmed')
        if proof['status']=='observed_zero' and (not isinstance(proof.get('counts'),dict)
                or set(proof['counts'])!={'owned_jobs','paired','reasoning','remote'}
                or any(type(v) is not int or v!=0 for v in proof['counts'].values())):
            return self._refusal('reservation_release_unconfirmed')
        with self.lock: media=tuple(self.observed_media)
        for mid in media:
            try:self._call(self.api,'GET',f"/sessions/{owner['session_id']}/media/{mid}/descriptor?generation={owner['generation']}")
            except Exception as error:
                if getattr(error,'code',None)==410 or getattr(error,'status',None)==410:continue
                raise
            return self._refusal('observed_media_not_withdrawn')
        with self.lock:
            if self.clock()>=self.cleanup_deadline or self.finished.is_set():return False
            self.state.update(cleanup_proof=deepcopy(proof),media_withdrawn=list(media),final_session_state=row['state'],
                ram_status='retired_by_verified_source_contract_and_media_withdrawal',cleanup_reason=None,
                cleanup_originals=dict(terminal_live=deepcopy(live),capabilities=deepcopy(caps),
                    worker_events=[e for e in events if e['kind'] in {'workers.cleanup','workers.local_drained'}]))
        return True

    def _refusal(self, reason):
        with self.lock:self.state['cleanup_reason']=reason
        return False

    def arm(self):
        with self.lock:
            if self.armed:raise ValueError('watchdog_is_single_use')
            self.armed=True;self.started=self.clock();self.deadline=self.started+90;self.stop_at=self.started+89;self.cleanup_deadline=self.started+120
            self.state.update(status='armed',armed_at=self.started,stop_initiate_at=self.stop_at,
                stop_deadline=self.deadline,cleanup_deadline=self.cleanup_deadline)
        self.stop_thread=threading.Thread(target=self._supervise,name='home-viewing-stop-watchdog',daemon=True)
        self.cutoff_thread=threading.Thread(target=self._cutoff,name='home-viewing-cleanup-deadline',daemon=True)
        self.binding_thread=threading.Thread(target=self._bind_worker,name='home-viewing-binding',daemon=True)
        self.cutoff_thread.start()
        self.binding_thread.start()
        self.stop_thread.start()
        self.collector=None
        if self.retain is not None:
            self.collector=threading.Thread(target=self._collect,name='home-viewing-evidence',daemon=True);self.collector.start()
        return self.snapshot()

    def _complete(self, confirmed):
        with self.lock:
            if self.state['finished']:return
            confirmed=confirmed and self.clock()<self.cleanup_deadline
            self.state.update(status='stopped' if confirmed else 'stop_failed',
                outcome='cleanup_confirmed' if confirmed else 'cleanup_unconfirmed',
                cleanup_confirmed=confirmed,finished=True)
        self.finished.set()

    def _cutoff(self):
        # This lane never calls API, file, probe, retention or notification ports.
        while self.clock()<self.cleanup_deadline and not self.finished.is_set():
            self.pause(min(.05,max(0,self.cleanup_deadline-self.clock())))
        if not self.finished.is_set():self._complete(False)

    def _bind_worker(self):
        # File/UUID discovery never blocks the independent Stop/deadline lanes.
        while self.clock()<self.cleanup_deadline and not self.finished.is_set():
            try:
                self._binding()
            except Exception as error:
                self._error(error)
                with self.lock:self.evidence_error='binding_refused'
                self.abort.set()
            try:self._resolve(self.evidence_api)
            except Exception as error:self._error(error)
            self.pause(.25)

    def _supervise(self):
        try:
            while self.clock()<self.stop_at and not self.abort.is_set():
                self.pause(min(.05,max(0,self.stop_at-self.clock())))
            with self.lock:self.state['status']='stopping'
            next_stop=self.clock()
            while self.clock()<self.cleanup_deadline and not self.finished.is_set():
                try:
                    with self.lock:owner=deepcopy(self.owner) if self.verified else None
                    if owner:
                        if self.clock()>=next_stop and self.state['stop_attempts']<3:
                            with self.lock:
                                self.state['stop_attempts']+=1
                                if self.state['stop_started_at'] is None:
                                    self.state['stop_started_at']=self.clock();self.state['stop_deadline_met']=self.clock()<=self.deadline
                            next_stop=self.clock()+2
                            try:self._call(self.api,'POST',f"/sessions/{owner['session_id']}/stop",{'request_id':owner['request_id']})
                            except Exception as error:self._error(error)
                        if self._cleanup(owner):
                            self._complete(True)
                            return
                except Exception as error:self._error(error)
                self.pause(min(.25,max(0,self.cleanup_deadline-self.clock())))
        finally:
            self._complete(False)

    @staticmethod
    def _registered_frame(live, owner):
        # The frozen /live contract; anything else is simply not sampled.
        metric=live.get('apartment_viewing') if isinstance(live,dict) else None
        metric=metric.get('metric') if isinstance(metric,dict) else None
        value=metric.get('registered_frame') if isinstance(metric,dict) else None
        if (not isinstance(value,dict) or set(value)!=REGISTERED_FRAME_KEYS
                or not isinstance(value['media_id'],str) or not ID.fullmatch(value['media_id']) or not value['media_id'].startswith('view_')
                or not isinstance(value['frame_id'],str) or not 0<len(value['frame_id'])<=256
                or not isinstance(value['media_sha256'],str) or not SHA.fullmatch(value['media_sha256'])
                or type(value['generation']) is not int or value['generation']!=owner['generation']
                or value['event_id'] is not None and (type(value['event_id']) is not int or value['event_id']<=0)):
            return None
        return deepcopy(value)

    def _sample_metric_png(self, owner, row, live):
        """Fetch an announced metric PNG at once, inside the shared image cap."""
        registered=self._registered_frame(live,owner)
        if registered is None:return
        mid=registered['media_id']
        with self.lock:
            remaining=METRIC_PNG_BYTES-self.metric_png_bytes
            if (mid in self.sample_attempts or len(self.metric_png_attempts)>=2*self.metric_pngs or len(self.sample_attempts)>=90
                    or len(self.metric_png_ids)>=self.metric_pngs or len(self.image_ids)>=self.max_images
                    or remaining<=0 or self.sampling_exhausted or row['state']!='recording' or self.clock()>=self.deadline):
                return
            # The arm-B lane later sees the same media id and skips it.
            self.sample_attempts.add(mid);self.metric_png_attempts.add(mid)
        selected=dict(media_id=mid,id=registered['frame_id'],site_id='victoria',generation=registered['generation'])
        try:sample=self.image_reader(deepcopy(owner),selected,limit=remaining)
        except SampleUnavailable as error:
            diagnostic=dict(error.diagnostic,media_id=mid,outcome='missed',lane='metric_png')
            with self.lock:
                self.missed_images+=1;self.media_http.append(diagnostic);self.sampling_exhausted=self.sampling_exhausted or error.permanent
            self._retain('image_missed',diagnostic)
            return
        if self.clock()>=self.deadline:return
        raw=sample.get('bytes');descriptor=sample.get('descriptor',{});meta=descriptor.get('metadata',{})
        digest=hashlib.sha256(raw).hexdigest() if isinstance(raw,bytes) else None
        if (not isinstance(raw,bytes) or not raw or len(raw)>remaining or digest!=registered['media_sha256']
                or descriptor.get('sha256')!=digest or (descriptor.get('id') or descriptor.get('media_id'))!=mid
                or descriptor.get('session_id')!=owner['session_id'] or descriptor.get('generation')!=owner['generation']
                or descriptor.get('site_id')!='victoria' or meta.get('site_id')!='victoria' or meta.get('camera_id')!='den'
                or meta.get('frame_id')!=registered['frame_id'] or sample.get('type')!='image/png'):
            raise ValueError('exact_registered_metric_png_required')
        if self.image_bytes+len(raw)>CAP:raise ValueError('image_budget_exhausted')
        with self.lock:
            self.image_bytes+=len(raw);self.image_ids.add(mid);self.observed_media.add(mid)
            self.metric_png_ids.add(mid);self.metric_png_bytes+=len(raw)
        self._retain('image',dict(media_id=mid,descriptor=descriptor,bytes=raw,type='image/png',lane='metric_png',
            registered_frame=registered,observed_at=self.clock()))

    def _retain(self, kind, value):
        metadata={k:v for k,v in value.items() if k!='bytes'} if kind=='image' else value
        size=len(encoded(dict(kind=kind,value=metadata)))+1
        # Reserve four MiB of the total metadata budget for terminal originals.
        if self.metadata_bytes+size>CAP-4*1024**2:raise ValueError('metadata_budget_exhausted')
        with self.lock:self.metadata_bytes+=size
        self.retain(kind,value)

    def _collect(self):
        try:
            while self.clock()<self.deadline and not self.finished.is_set() and not self.abort.is_set():
                self._binding()
                if self.clock()>=self.deadline or self.finished.is_set():break
                owner=self._resolve(self.evidence_api)
                if self.clock()>=self.deadline or self.finished.is_set():break
                if owner:
                    live=self._call(self.evidence_api,'GET',f"/sessions/{owner['session_id']}/live")
                    if self.clock()>=self.deadline or self.finished.is_set():break
                    row=self._session(live.get('session'),owner)
                    if self.metric_pngs and self.image_reader:
                        self._sample_metric_png(owner,row,live)
                        if self.clock()>=self.deadline or self.finished.is_set():break
                    arm=live.get('arms',{}).get('B',{});frame=arm.get('frame',{});observation=arm.get('observation',{})
                    mid=frame.get('media_id')
                    exact=(isinstance(mid,str) and ID.fullmatch(mid) and frame.get('id')==arm.get('frame_id')
                        and frame.get('site_id')=='victoria' and frame.get('generation')==owner['generation']
                        and observation.get('schema')=='home-lab-arm-observation-1' and observation.get('frame_id')==arm.get('frame_id')
                        and observation.get('session_id')==owner['session_id'] and observation.get('arm')=='b'
                        and observation.get('site_id')=='victoria' and observation.get('camera_id')=='den'
                        and isinstance(observation.get('capture_epoch'),str) and 0<len(observation['capture_epoch'])<=256
                        and isinstance(observation.get('media_ids'),list) and 1<=len(observation['media_ids'])<=8
                        and mid in observation['media_ids'])
                    if exact:
                        with self.lock:self.observed_media.add(mid)
                    if (exact and self.image_reader and mid not in self.sample_attempts and len(self.sample_attempts)<90
                            and len(self.image_ids)<self.max_images and not self.sampling_exhausted
                            and row['state']=='recording' and self.clock()<self.deadline):
                        self.sample_attempts.add(mid);selected=deepcopy(frame);selected['capture_epoch']=observation['capture_epoch']
                        try:sample=self.image_reader(deepcopy(owner),selected)
                        except SampleUnavailable as error:
                            diagnostic=dict(error.diagnostic,media_id=mid,outcome='missed')
                            with self.lock:
                                self.missed_images+=1;self.media_http.append(diagnostic);self.sampling_exhausted=error.permanent
                            self._retain('image_missed',diagnostic);sample=None
                        if self.clock()>=self.deadline:break
                        if sample is not None:
                            raw=sample.get('bytes');descriptor=sample.get('descriptor',{});meta=descriptor.get('metadata',{})
                            digest=hashlib.sha256(raw).hexdigest() if isinstance(raw,bytes) else None
                            if (not isinstance(raw,bytes) or not raw or descriptor.get('sha256')!=digest
                                    or (descriptor.get('id') or descriptor.get('media_id'))!=mid
                                    or descriptor.get('session_id')!=owner['session_id'] or descriptor.get('generation')!=owner['generation']
                                    or descriptor.get('site_id')!='victoria' or meta.get('site_id')!='victoria' or meta.get('camera_id')!='den'
                                    or meta.get('frame_id')!=arm['frame_id'] or meta.get('capture_epoch')!=observation.get('capture_epoch')
                                    or sample.get('type') not in {'image/jpeg','image/png'}):
                                raise ValueError('exact_sampled_image_original_required')
                            if self.image_bytes+len(raw)>CAP:raise ValueError('image_budget_exhausted')
                            with self.lock:self.image_bytes+=len(raw);self.image_ids.add(mid)
                            self._retain('image',dict(media_id=mid,descriptor=descriptor,bytes=raw,type=sample['type'],observation=observation))
                    if self.clock()>=self.deadline or self.finished.is_set():break
                    self._retain('live',live)
                    if self.clock()>=self.deadline or self.finished.is_set():break
                    self._retain('events',self._events(self.evidence_api,owner))
                self.pause(1)
        except Exception as error:
            with self.lock:
                if isinstance(error,MediaFailure) and len(self.media_http)<100:self.media_http.append(dict(error.diagnostic,outcome='fatal'))
                self.evidence_error=str(error) if isinstance(error,ValueError) else type(error).__name__
            self.abort.set()


class PrivateEvidence:
    def __init__(self,directory):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700)
        self.fd=os.open(self.directory/'metadata.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        self.bytes=0;self.count=0;self.digest=hashlib.sha256()
    def __call__(self,kind,value):
        if kind=='image':
            raw=value['bytes'];digest=hashlib.sha256(raw).hexdigest();name=value['media_id']+'-'+digest+('.jpg' if value['type']=='image/jpeg' else '.png')
            fd=os.open(self.directory/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
            value={k:v for k,v in value.items() if k!='bytes'};value.update(file=name,sha256=digest,size=len(raw))
        raw=encoded(dict(kind=kind,value=value))+b'\n'
        if self.bytes+len(raw)>CAP-4*1024**2:raise ValueError('private_metadata_budget_exhausted')
        offset=0
        while offset<len(raw):
            count=os.write(self.fd,raw[offset:])
            if count<=0:raise OSError('evidence_write_failed')
            offset+=count
        os.fsync(self.fd)
        self.bytes+=len(raw);self.count+=1;self.digest.update(raw)

    def receipt(self):
        return dict(file='metadata.jsonl',bytes=self.bytes,records=self.count,sha256=self.digest.hexdigest())


class LedgerCleanupProbe:
    """Read an actual consistent ledger snapshot; never clear a reservation.

    Source pins come from the verified deployment, not from the owner typing a
    claim. Missing tables, changed code, foreign scope or malformed originals
    refuse. RAM heap size is deliberately not measured or asserted here.
    """
    SOURCES={'managed_pair.py','service_runtime.py','apartment_viewing_source.py','runtime.py',
        'remote_specialist.py','live_reasoning.py','jev_policy.py','app.py','store.py'}

    def __init__(self, ledger_file, source_pins):
        self.path=Path(ledger_file)
        if not self.path.is_absolute() or self.path.is_symlink() or not self.path.is_file():
            raise ValueError('existing_actual_readonly_ledger_required')
        if (not isinstance(source_pins,dict) or not source_pins or len(source_pins)>32
                or not self.SOURCES.issubset({Path(p).name for p in source_pins})
                or any(not Path(p).is_absolute() or not isinstance(s,str) or not SHA.fullmatch(s) for p,s in source_pins.items())):
            raise ValueError('verified_deployment_source_pins_required')
        self.pins=deepcopy(source_pins)

    def __call__(self, owner):
        owner=binding(owner)
        for name,expected in self.pins.items():
            path=Path(name)
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1024**2 or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('cleanup_source_contract_changed')
        if self.path.is_symlink():raise ValueError('actual_ledger_changed')
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.2)
        db.row_factory=sqlite3.Row
        aggregate=hashlib.sha256();counts={k:0 for k in ('owned_jobs','paired','reasoning','remote')}
        try:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            row=db.execute('SELECT * FROM sessions WHERE id=?',(owner['session_id'],)).fetchone()
            if (row is None or any(row[k]!=owner[k] for k in ('request_id','site_id','camera_id','experiment_id','generation'))
                    or row['state'] not in TERMINAL):
                raise ValueError('terminal_actual_ledger_scope_required')
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'paired_requests','remote_specialist_dispatch','lab_reasoning_requests','jobs'}.issubset(tables):
                raise ValueError('reservation_inventory_unavailable')
            for table,scope,allowed,label in (
                ('paired_requests','session_id',{'completed','rejected','cancelled','dropped','expired'},'paired'),
                ('lab_reasoning_requests','session_id',{'completed','failed','expired','cancelled','replaced','rejected'},'reasoning'),
                ('jobs','session_id',{'blocked','completed','failed','cancelled','interrupted'},'owned_jobs')):
                rows=db.execute(f'SELECT id,state FROM {table} WHERE {scope}=? ORDER BY id LIMIT 6001',(owner['session_id'],)).fetchall()
                if len(rows)>6000:raise ValueError('bounded_reservation_inventory_required')
                for item in rows:
                    aggregate.update(encoded(dict(table=table,id=item['id'],state=item['state'])))
                    if item['state'] not in allowed:counts[label]+=1
            rows=db.execute("""SELECT d.request_id,d.reference,p.state,p.result,p.payload FROM remote_specialist_dispatch d
                LEFT JOIN paired_requests p ON p.id=d.request_id
                WHERE p.session_id=? OR json_extract(d.reference,'$.session_id')=? ORDER BY d.request_id LIMIT 6001""",
                (owner['session_id'],owner['session_id']))
            size=0;number=0
            for item in rows:
                number+=1
                if number>6000:raise ValueError('bounded_remote_inventory_required')
                size+=sum(len(item[k] or '') for k in ('reference','result','payload'))
                if size>128*1024**2:raise ValueError('bounded_remote_originals_required')
                try:
                    reference=json.loads(item['reference']);receipt=json.loads(item['result']);request=json.loads(item['payload'])
                    profile=request['profile']
                    affirmative=(item['state'] in {'completed','rejected','cancelled'} and receipt.get('terminal') is True
                        and receipt.get('state')==item['state'] and receipt.get('request_id')==item['request_id']
                        and reference.get('request_id')==item['request_id'] and reference.get('session_id')==owner['session_id']
                        and request.get('session_id')==owner['session_id']
                        and all(receipt.get(k)==profile.get(k) and isinstance(profile.get(k),str)
                            for k in ('engine_epoch','artifact_sha256')))
                except (ValueError,TypeError,KeyError):affirmative=False
                if not affirmative:counts['remote']+=1
                aggregate.update(encoded(dict(request_id=item['request_id'],affirmative=affirmative,
                    original_sha256=hashlib.sha256((item['reference']+'\n'+(item['result'] or '')+'\n'+(item['payload'] or '')).encode()).hexdigest())))
        finally:db.close()
        return dict(schema='home-viewing-reservation-release-1',version=1,
            **{k:owner[k] for k in ('request_id','session_id','generation')},counts=counts,
            status='observed_zero' if not any(counts.values()) else 'unresolved',unresolved=any(counts.values()),
            release_scopes=['owned_jobs','paired','reasoning','remote'],evidence_sha256=[aggregate.hexdigest()],
            source_contract_sha256=hashlib.sha256(encoded(self.pins)).hexdigest(),
            source_files=deepcopy(self.pins),ledger_path=str(self.path),
            method='scoped_readonly_consistent_controller_ledger_and_affirmative_remote_receipts')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-owner-ready',action='store_true');parser.add_argument('--owner-ready',action='store_true')
    parser.add_argument('--binding-file',type=Path)
    parser.add_argument('--ledger-file',type=Path);parser.add_argument('--source-pins-file',type=Path)
    parser.add_argument('--private-directory',type=Path);parser.add_argument('--token-file',type=Path);parser.add_argument('--ca-file',type=Path)
    parser.add_argument('--sample-images',type=int,default=8)
    parser.add_argument('--metric-pngs',type=int,default=0)
    args=parser.parse_args(argv)
    if not 0<=args.sample_images<=90:parser.error('sample-images must be between zero and ninety')
    if not 0<=args.metric_pngs<=min(METRIC_PNG_MAX,args.sample_images):
        parser.error('metric-pngs must be between zero and six, and within sample-images')
    if not args.execute_owner_ready:
        print(json.dumps(dict(status='prepared_only',network_requests=0,starts_sessions=False,operator_seconds=90,cleanup_seconds=30,
            binding_schema='home-viewing-watchdog-binding-1',sample_images=args.sample_images,image_bytes=CAP,metadata_bytes=CAP,
            metric_pngs=args.metric_pngs,metric_png_bytes=METRIC_PNG_BYTES)))
        return 0
    if (not args.owner_ready or not all((args.ledger_file,args.source_pins_file,args.binding_file,args.private_directory,args.token_file,args.ca_file))
            or not all(p.is_absolute() for p in (args.binding_file,args.private_directory,args.token_file,args.ca_file,args.ledger_file,args.source_pins_file))
            or not args.private_directory.parent.is_dir() or not 0<=args.sample_images<=90):
        parser.error('fresh owner-ready window, absolute private paths, protected token/CA and bounded samples required')
    if args.binding_file.exists():raise ValueError('fresh_unbound_binding_required')
    # Existing fixed TLS/auth pattern; it offers no Start route through Watchdog.
    source=Path(__file__).with_name('lab_whole_stack_trial.py')
    if hashlib.sha256(source.read_bytes()).hexdigest()!='6ddf956d0c357a251d5562ddd44e3ae8429b432d6d20877b4965548ee830f7b3':
        raise ValueError('exact_incumbent_auth_source_required')
    spec=importlib.util.spec_from_file_location('home_viewing_auth',source);whole=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=whole;spec.loader.exec_module(whole)
    api=whole.LabHTTP(token_file=args.token_file,ca_file=args.ca_file)
    evidence_api=whole.LabHTTP(token_file=args.token_file,ca_file=args.ca_file)
    retain=PrivateEvidence(args.private_directory)
    whole.sync_parent(args.private_directory.parent)
    pins=read_private_json(args.source_pins_file,32768)
    if not isinstance(pins,dict) or pins.get('schema')!='home-viewing-cleanup-source-pins-1' or type(pins.get('version')) is not int or pins['version']!=1:
        raise ValueError('verified_deployment_source_pins_required')
    cleanup_probe=LedgerCleanupProbe(args.ledger_file,pins.get('files'))
    def image_reader(owner,frame,limit=None):
        return read_sampled_image(evidence_api,whole,watchdog,owner,frame,time.monotonic,limit=limit)
    watchdog=Watchdog(api,lambda:read_private_json(args.binding_file),cleanup_probe,
        retain=retain,evidence_api=evidence_api,image_reader=image_reader,max_images=args.sample_images,
        metric_pngs=args.metric_pngs)
    print(json.dumps(watchdog.arm(),sort_keys=True),flush=True)
    while not watchdog.finished.is_set():
        try:watchdog.finished.wait(.25)
        except KeyboardInterrupt:watchdog.abort.set()
    if watchdog.collector:watchdog.collector.join(timeout=max(0,min(1,watchdog.cleanup_deadline-time.monotonic())))
    result=watchdog.snapshot();result['evidence_worker_pending']=bool(watchdog.collector and watchdog.collector.is_alive())
    result['supervision_worker_pending']=watchdog.stop_thread.is_alive() or watchdog.binding_thread.is_alive()
    result['evidence_complete']=not result['evidence_error'] and not result['evidence_worker_pending'] and result['image_evidence_complete']
    result['evidence_archive']=retain.receipt() if not result['evidence_worker_pending'] else {'status':'unfinalized'}
    raw=encoded(result)+b'\n'
    if len(raw)>4*1024**2:
        result.pop('cleanup_originals',None);result['evidence_complete']=False;result['evidence_error']='terminal_originals_budget_exhausted'
        raw=encoded(result)+b'\n'
    fd=os.open(args.private_directory/'result.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
    if not result['evidence_worker_pending']:os.close(retain.fd)
    whole.sync_parent(args.private_directory)
    print(json.dumps(result,sort_keys=True),flush=True)
    return 0 if result['cleanup_confirmed'] and result['evidence_complete'] else 1


if __name__=='__main__':raise SystemExit(main())
