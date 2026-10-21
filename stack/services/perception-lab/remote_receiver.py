"""Durable remote execution core; an authenticated ingress must bind source_site.

No network listener or model load occurs here. A dedicated, owned Store supplies
the ledger. Admitted execution and media grants remain provisioned dependencies.
"""
import base64
from copy import deepcopy
import hashlib
import io
import json
from reasoning import canonical, digest, finite
from keypoint_worker import KeypointWorker
from yolox_worker import YoloXWorker
from ultralytics_worker import UltralyticsWorker
from rtmpose_worker import RTMPoseWorker
from staged_specialist import DetectorPoseWorker


class ExecutionNotFound(ValueError):
    """Source admitted and exact resident profile matched; no such execution row.

    Proves only profile readiness, never that an execution did not or will not occur.
    """
    profile_admitted=True


class RemoteReceiver:
    def __init__(self, store, worker, profile, authorize, *, viewing_jpeg=False):
        if not isinstance(worker,(KeypointWorker,YoloXWorker,UltralyticsWorker,RTMPoseWorker,DetectorPoseWorker)) or not callable(authorize):
            raise ValueError('fixed admitted worker required')
        if isinstance(worker,DetectorPoseWorker) and (worker.store is not store or worker.reservation_kind!='remote'):
            raise ValueError('composite worker must own this remote execution ledger')
        if profile != {**worker.manifest,'artifact_sha256':worker.manifest['sha256']}:
            raise ValueError('exact worker profile required')
        self.store,self.worker,self.profile,self.authorize=store,worker,deepcopy(profile),authorize
        self.viewing_jpeg=viewing_jpeg is True
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS remote_execution (site TEXT NOT NULL, id TEXT NOT NULL, body_sha TEXT NOT NULL, reference TEXT NOT NULL, state TEXT NOT NULL, proof TEXT, PRIMARY KEY(site,id))')
            db.execute('CREATE TABLE IF NOT EXISTS remote_inputs (site TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(site,id))')

    def _reference(self,envelope,source_site):
        reference={k:v for k,v in envelope.items() if k!='body'}
        if (set(reference)!={'request_id','body_sha256','site_id','camera_id','session_id','profile'}
                or reference['site_id']!=source_site or reference['profile']!=self.profile
                or self.profile!={**self.worker.manifest,'artifact_sha256':self.worker.manifest['sha256']}
                or self.authorize('source',deepcopy(reference)) is not True):
            raise ValueError('source or worker not admitted')
        return reference

    def lookup(self,reference,*,source_site):
        reference=self._reference(reference,source_site)
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM remote_execution WHERE site=? AND id=?',
                (source_site,reference['request_id'])).fetchone()
        if not row or json.loads(row['reference'])!=reference:
            raise ExecutionNotFound('matching remote execution not found')
        if row['state']!='terminal':
            raise ValueError('remote execution completion indeterminate')
        return json.loads(row['proof'])

    def execute(self,envelope,*,source_site):
        # HTTP layer must enforce a body limit before parsing, too.
        if len(canonical(envelope))>6*1024**2: raise ValueError('remote envelope exceeds bound')
        reference=self._reference(envelope,source_site)
        body=envelope['body']; request=body['request']; frame=request['frame']; image=body['image']
        if (body.get('schema_version')!=1 or digest(body)!=reference['body_sha256']
                or request.get('request_id')!=reference['request_id'] or request.get('profile')!=self.profile
                or frame.get('site_id')!=source_site or frame.get('camera_id')!=reference['camera_id']
                or frame.get('session_id')!=reference['session_id']):
            raise ValueError('remote body binding mismatch')
        with self.store.lock:
            prior=self.store.db.execute('SELECT 1 FROM remote_execution WHERE site=? AND id=?',
                (source_site,reference['request_id'])).fetchone()
        if prior: return self.lookup(reference,source_site=source_site)
        # A valid frame may expire in transit. Validate its binding/integrity,
        # then persist the terminal cancellation below without invoking a model.
        # An HTTP validation error supplies no completion proof to the sender.
        if (not finite(frame.get('valid_until'))
                or image.get('encoding') not in ({'png','jpeg'} if self.viewing_jpeg else {'png'}) or image.get('channel_order')!='RGB'
                or image.get('decoded_worker_order')!='BGR'
                or image.get('source_media_id') not in frame.get('media_ids',[])
                or type(image.get('width')) is not int or not 0<image['width']<=3840
                or type(image.get('height')) is not int or not 0<image['height']<=2160):
            raise ValueError('invalid remote image contract')
        data=base64.b64decode(image['data_base64'],validate=True)
        if not 0<len(data)<=4*1024**2 or hashlib.sha256(data).hexdigest()!=image['encoded_sha256']:
            raise ValueError('remote encoded image integrity failure')
        from PIL import Image
        import numpy as np
        with Image.open(io.BytesIO(data)) as decoded:
            if decoded.format!=('JPEG' if image['encoding']=='jpeg' else 'PNG') or decoded.size!=(image['width'],image['height']) or getattr(decoded,'n_frames',1)!=1:
                raise ValueError('remote image dimensions/type mismatch')
            pixels=np.ascontiguousarray(np.asarray(decoded.convert('RGB'),dtype=np.uint8)[:,:,::-1])
        if (image['encoding']=='jpeg' and image.get('bgr_sha256') is not None
                or image['encoding']=='png' and hashlib.sha256(pixels.tobytes()).hexdigest()!=image['bgr_sha256']):
            raise ValueError('remote decoded pixel integrity failure')
        crops=None
        if isinstance(self.worker,RTMPoseWorker):
            from remote_crops import pose_bundle
            crops=pose_bundle(body.get('pose_crops'),frame,self.profile['max_people'])
        elif 'pose_crops' in body:
            raise ValueError('unexpected pose crop input')
        if self.authorize('execute',deepcopy(reference)) is not True:
            raise ValueError('remote execution not admitted')
        with self.store.transaction() as db:
            self.store.check_capacity(512*1024)
            # A concurrent duplicate loses the unique insert and cannot execute.
            db.execute('INSERT INTO remote_execution VALUES(?,?,?,?,?,NULL)',
                (source_site,reference['request_id'],reference['body_sha256'],canonical(reference),'dispatching'))
            if isinstance(self.worker,DetectorPoseWorker):
                # Store only the exact request metadata, never encoded image
                # bytes. This binds both stages to one durable reservation.
                db.execute('INSERT INTO remote_inputs VALUES(?,?,?)',
                    (source_site,reference['request_id'],canonical(request)))
        def load(offered):
            if offered!=frame: raise ValueError('worker changed remote frame')
            return pixels,{**frame,'media_id':image['source_media_id']}
        try:
            if self.store.clock()>frame['valid_until']:
                proof={'request_id':reference['request_id'],'terminal':True,'state':'cancelled',
                    'engine_epoch':self.profile['engine_epoch'],'artifact_sha256':self.profile['artifact_sha256'],
                    'result':{'reason':'source_expired_before_execution'}}
            else:
                proof=(self.worker.execute(deepcopy(request),load,lambda _:deepcopy(crops))
                       if isinstance(self.worker,RTMPoseWorker) else self.worker.execute(deepcopy(request),load))
            if (proof.get('request_id')!=reference['request_id'] or proof.get('terminal') is not True
                    or proof.get('state') not in {'completed','rejected','cancelled'}
                    or proof.get('engine_epoch')!=self.profile['engine_epoch']
                    or proof.get('artifact_sha256')!=self.profile['artifact_sha256']):
                raise ValueError('worker completion unproven')
            proof={**proof,'body_sha256':reference['body_sha256']}
            if len(canonical(proof))>256*1024: raise ValueError('remote result exceeds bound')
            with self.store.transaction() as db:
                db.execute("UPDATE remote_execution SET state='terminal',proof=? WHERE site=? AND id=?",
                    (canonical(proof),source_site,reference['request_id']))
            return proof
        except Exception as error:
            import logging, traceback
            from pathlib import Path
            locations=[(Path(f.filename).name,f.lineno,f.name) for f in traceback.extract_tb(error.__traceback__)[-4:]]
            logging.getLogger(__name__).warning('Remote execution uncertain: %s at %s',type(error).__name__,locations)
            # Even after process restart dispatching rows remain nonterminal.
            # No timeout or lost connection releases admission or permits retry.
            with self.store.transaction() as db:
                db.execute("UPDATE remote_execution SET state='indeterminate' WHERE site=? AND id=?",
                    (source_site,reference['request_id']))
            raise
