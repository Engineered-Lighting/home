"""Exact-pixel remote specialist envelope through a provisioned admission transport.

No endpoint, credentials, model loading or implicit fallback is supplied here.
Transport must enforce workload admission and permitted media movement.
"""
import base64
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import io
import json
import re
import threading
import time
import weakref
from reasoning import canonical, digest
from registered_image import RegisteredImage
from remote_transport import NotExecuted, PRE_EXECUTION
from runtime import CompletionUnproven, LookupNotPerformed

BUSY={reason for status,_,reason in PRE_EXECUTION if status==429}

# Both arms receive one frame together. Share its decode, PNG and hashes; the
# bytes are identical, so worker checks are unchanged. Two frames per ledger.
_ENCODED=weakref.WeakKeyDictionary()
_ENCODED_LOCK=threading.Lock()


def retire_viewing_images(store,session_id):
    """Release only the ended viewing session's bounded encode-cache entries."""
    with _ENCODED_LOCK:
        entries=_ENCODED.get(store)
        if entries is not None:
            for key in list(entries):
                if key[0]==session_id:entries.pop(key,None)


class _Oversize(ValueError):
    pass


class RemoteSpecialist:
    def __init__(self, store, profile, transport, authorize, *, crop_loader=None):
        if (not callable(transport) or not callable(authorize)
                or not re.fullmatch(r'[a-f0-9]{64}',profile.get('artifact_sha256',''))
                or any(not isinstance(profile.get(k),str) or not profile[k] for k in
                       ('profile_id','model_version','engine_epoch','device_id','runtime_revision'))):
            raise ValueError('pinned remote profile and admission callbacks required')
        self.store,self.profile,self.transport,self.authorize=store,deepcopy(profile),transport,authorize
        from rtmpose_worker import CHECKPOINT
        from rtmw3d_worker import CHECKPOINT as RTMW_CHECKPOINT
        if (profile.get('checkpoint') in {CHECKPOINT,RTMW_CHECKPOINT}) != callable(crop_loader):
            raise ValueError('remote RTMPose requires a registered crop resolver')
        self.crop_loader=crop_loader
        self.images=RegisteredImage(store)
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS remote_specialist_dispatch (request_id TEXT PRIMARY KEY, reference TEXT NOT NULL)')

    def __call__(self, request):
        request=deepcopy(request)
        if request.get('profile') != self.profile:
            raise ValueError('remote specialist profile mismatch')
        frame=request['frame']; sid=frame['session_id']
        cancelled=self._cancel_before_dispatch(request)
        if cancelled is not None:return cancelled
        if self.authorize('prepare_media',deepcopy(request)) is not True:
            return self._rejected_before_dispatch(request,'media_not_authorized')
        started=time.monotonic()
        try:
            image=self._image(frame)
        except Exception as error:
            # Local read/integrity/decode/bound failure; no byte has left.
            return self._rejected_before_dispatch(request,'encoded_frame_exceeds_bound'
                if isinstance(error,_Oversize) else 'registered_media_unavailable')
        body={'schema_version':1,'request':request,'image':image}
        if self.crop_loader is not None:
            from remote_crops import pose_bundle
            body['pose_crops']=pose_bundle(self.crop_loader(deepcopy(frame)),frame,self.profile['max_people'])
        encoded=canonical(body)
        reference={'request_id':request['request_id'],'body_sha256':hashlib.sha256(encoded.encode()).hexdigest(),
                   'site_id':frame['site_id'],'camera_id':frame['camera_id'],'session_id':sid,'profile':deepcopy(self.profile)}
        timing={'encode_ms':(time.monotonic()-started)*1000,'body_bytes':len(encoded)}
        if self.authorize('dispatch',deepcopy(reference)) is not True:
            return self._rejected_before_dispatch(request,'dispatch_not_authorized')
        with self.store.transaction() as db:
            session=self.store.get_session(sid)
            parent=db.execute('SELECT state,payload FROM paired_requests WHERE id=?',(request['request_id'],)).fetchone()
            if (not parent or parent['state'] not in {'dispatching','running'}
                    or json.loads(parent['payload'])!=request):
                raise ValueError('active matching paired reservation required')
            if db.execute('SELECT 1 FROM remote_specialist_dispatch WHERE request_id=?',(request['request_id'],)).fetchone():
                raise ValueError('remote dispatch already attempted; reconcile only')
            if session['state']!='recording':
                return self._cancelled(request,'session_stopped_before_dispatch')
            if self.store.clock()>frame['valid_until']:
                return self._cancelled(request)
            self.store.check_capacity(len(canonical(reference))+65536)
            db.execute('INSERT INTO remote_specialist_dispatch VALUES(?,?)',(request['request_id'],canonical(reference)))
        # Never retry or resend. Unproven completion stays indeterminate unless
        # a bounded identity-only lookup later returns matching terminal proof.
        sent=time.monotonic()
        try:
            proof=self.transport({**reference,'body':body})
        except NotExecuted as error:
            if error.reason not in {reason for _,_,reason in PRE_EXECUTION}:
                raise self._unproven(request['request_id']) from None
            receipt=self._terminal(request,'rejected','worker_'+error.reason)
        except Exception:
            raise self._unproven(request['request_id']) from None
        else:
            try:
                receipt=self._receipt(reference,proof)
            except ValueError:
                raise self._unproven(request['request_id']) from None
        receipt['delivery_timing']={**timing,'transport_ms':(time.monotonic()-sent)*1000}
        return receipt

    def _image(self, frame):
        key=(frame['session_id'],digest(frame))
        with _ENCODED_LOCK:
            entries=_ENCODED.setdefault(self.store,OrderedDict())
            entry=entries.get(key)
            owner=entry is None
            if owner:
                entry=entries[key]=Future()
                while len(entries)>2: entries.popitem(last=False)
            else:
                entries.move_to_end(key)
        if owner:
            try:
                entry.set_result(self._encode(frame))
            except BaseException as error:
                entry.set_exception(error)
                with _ENCODED_LOCK:
                    if entries.get(key) is entry: del entries[key]
        return dict(entry.result())

    def _encode(self, frame):
        pixels,metadata=self.images(frame)
        from PIL import Image
        encoded=io.BytesIO()
        # Lossless pixels and the decoded hash are unchanged. The default PNG
        # compression spends scarce controller CPU on bandwidth saving while
        # both arms wait against a short source-freshness deadline.
        Image.fromarray(pixels[:,:,::-1]).save(encoded,format='PNG',compress_level=1)
        data=encoded.getvalue()
        if len(data)>4*1024**2:
            raise _Oversize('remote encoded frame exceeds bound')
        return {'encoding':'png','channel_order':'RGB','decoded_worker_order':'BGR',
            'width':pixels.shape[1],'height':pixels.shape[0],
            'source_media_id':metadata['media_id'],
            'encoded_sha256':hashlib.sha256(data).hexdigest(),
            'bgr_sha256':hashlib.sha256(pixels.tobytes()).hexdigest(),
            'data_base64':base64.b64encode(data).decode('ascii')}

    def _terminal(self,request,state,reason):
        return {'request_id':request['request_id'],'terminal':True,'state':state,
                'engine_epoch':self.profile['engine_epoch'],
                'artifact_sha256':self.profile['artifact_sha256'],
                'result':{'reason':reason}}

    def _cancelled(self,request,reason='source_expired_before_dispatch'):
        return self._terminal(request,'cancelled',reason)

    def _unproven(self,request_id):
        lookup=getattr(self.transport,'lookup',None)
        if not callable(lookup):return CompletionUnproven(None)
        def identity():
            try:
                return self.reconcile(request_id,lookup)
            except NotExecuted as error:
                # Only an exact busy reply proves this lookup never ran; the
                # ingress stays busy until the orphaned execution ends.
                if error.reason in BUSY:raise LookupNotPerformed(error.reason) from None
                raise
        return CompletionUnproven(identity)

    def _unstarted(self,db,request):
        parent=db.execute('SELECT state,payload FROM paired_requests WHERE id=?',(request['request_id'],)).fetchone()
        if (not parent or parent['state'] not in {'dispatching','running'}
                or json.loads(parent['payload'])!=request
                or db.execute('SELECT 1 FROM remote_specialist_dispatch WHERE request_id=?',(request['request_id'],)).fetchone()):
            raise ValueError('no proof of unstarted remote dispatch')

    def _cancel_before_dispatch(self,request):
        from reasoning import finite
        frame=request['frame']
        with self.store.transaction() as db:
            stopped=self.store.get_session(frame['session_id'])['state']!='recording'
            expired=finite(frame.get('valid_until')) and self.store.clock()>frame['valid_until']
            if not stopped and not expired:return None
            self._unstarted(db,request)
            # The parent records this terminal receipt. No network reservation
            # exists, and no retry or remote lookup is required.
            return self._cancelled(request,'session_stopped_before_dispatch' if stopped else 'source_expired_before_dispatch')

    def _rejected_before_dispatch(self,request,reason):
        # Stop or expiry during preparation stays the more specific outcome.
        cancelled=self._cancel_before_dispatch(request)
        if cancelled is not None:return cancelled
        with self.store.transaction() as db:
            self._unstarted(db,request)
        return self._terminal(request,'rejected',reason)

    def _receipt(self, reference, proof):
        if (not isinstance(proof,dict) or proof.get('request_id')!=reference['request_id']
                or proof.get('body_sha256')!=reference['body_sha256']
                or proof.get('engine_epoch')!=self.profile['engine_epoch']
                or proof.get('artifact_sha256')!=self.profile['artifact_sha256']
                or proof.get('terminal') is not True
                or proof.get('state') not in {'completed','rejected','cancelled'}
                or len(canonical(proof))>256*1024):
            raise ValueError('matching remote terminal proof required')
        return {k:deepcopy(v) for k,v in proof.items() if k in
                {'request_id','engine_epoch','artifact_sha256','terminal','state','result'}}

    def reconcile(self, request_id, lookup):
        with self.store.lock:
            row=self.store.db.execute('SELECT reference FROM remote_specialist_dispatch WHERE request_id=?',(request_id,)).fetchone()
        if not row: raise ValueError('remote dispatch not registered')
        reference=json.loads(row[0])
        if reference['profile']!=self.profile: raise ValueError('remote profile changed')
        # Terminal lookup sends only identity/digest, never pixels or inference.
        return self._receipt(reference,lookup(deepcopy(reference)))
