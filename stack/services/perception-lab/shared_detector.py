"""One admitted source detector per exact frame, shared by paired pose arms."""
from copy import deepcopy
import json
import threading
from reasoning import digest,canonical,finite
from registered_image import RegisteredImage
from keypoint_worker import KeypointWorker
from yolox_worker import YoloXWorker
from ultralytics_worker import UltralyticsWorker


class SharedDetectorCrops:
    def __init__(self,store,worker,authorize,*,minimum_confidence,max_people,policy_revision):
        if (not isinstance(worker,(KeypointWorker,YoloXWorker,UltralyticsWorker)) or not callable(authorize)
                or type(max_people) is not int or not 1<=max_people<=16
                or not isinstance(policy_revision,str) or not 1<=len(policy_revision)<=128):
            raise ValueError('provisioned shared detector and crop policy required')
        if isinstance(worker,YoloXWorker):
            if minimum_confidence is not None: raise ValueError('YOLOX does not supply confidence')
        elif not finite(minimum_confidence) or not 0<=minimum_confidence<=1:
            raise ValueError('explicit detector threshold required')
        self.store,self.worker,self.authorize=store,worker,authorize
        self.manifest=deepcopy(worker.manifest)
        self.policy={'minimum_confidence':minimum_confidence,'max_people':max_people,'revision':policy_revision}
        self.images=RegisteredImage(store)
        self.lock=threading.RLock()
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shared_detector_stage (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, input_digest TEXT NOT NULL, state TEXT NOT NULL, bundle TEXT)')

    def __call__(self,frame):
        frame=deepcopy(frame)
        with self.lock:
            if self.worker.manifest!=self.manifest: raise ValueError('shared detector manifest changed')
            session=self.store.get_session(frame['session_id'])
            if session['state']!='recording' or not finite(frame.get('valid_until')) or self.store.clock()>frame['valid_until']:
                raise ValueError('fresh active recording required')
            with self.store.lock:
                parents=self.store.db.execute("SELECT payload FROM paired_requests WHERE session_id=? AND state IN ('dispatching','running')",(session['id'],)).fetchall()
            if not any(json.loads(row[0])['frame']==frame for row in parents):
                raise ValueError('active paired source request required')
            key=digest([*[frame[k] for k in ('site_id','session_id','camera_id','capture_epoch','frame_id')],self.manifest,self.policy])
            if self.authorize('source',deepcopy(self.manifest),key) is not True:
                raise ValueError('shared detector source not authorized')
            with self.store.transaction() as db:
                prior=db.execute('SELECT * FROM shared_detector_stage WHERE id=?',(key,)).fetchone()
                if prior:
                    if prior['input_digest']!=digest(frame):raise ValueError('shared source identity changed')
                    if prior['state']!='completed':raise ValueError('shared detector completion unproven')
                    return json.loads(prior['bundle'])
                self.store.check_capacity(256*1024)
                db.execute('INSERT INTO shared_detector_stage VALUES(?,?,?,?,NULL)',(key,session['id'],digest(frame),'dispatching'))
            try:
                pixels,meta=self.images(frame)
                if self.authorize('execute',deepcopy(self.manifest),key) is not True:
                    raise ValueError('shared detector not admitted')
                profile={**self.manifest,'artifact_sha256':self.manifest['sha256']}
                request={'request_id':key,'profile':profile,'frame':frame}
                if self.store.get_session(session['id'])['state']!='recording' or self.store.clock()>frame['valid_until']:
                    raise ValueError('shared input stopped or expired before execution')
                def load(source):
                    if source!=frame:raise ValueError('shared worker changed source')
                    return pixels,deepcopy(meta)
                proof=self.worker.execute(request,load)
                if (proof.get('request_id')!=key or proof.get('terminal') is not True or proof.get('state')!='completed'
                        or proof.get('engine_epoch')!=profile['engine_epoch'] or proof.get('artifact_sha256')!=profile['artifact_sha256']):
                    raise ValueError('shared detector completion unproven')
                result=proof['result'];height,width=pixels.shape[:2]
                if result.get('frame_id')!=frame['frame_id'] or result.get('coordinate_space')!='source_pixels' or not isinstance(result.get('boxes'),list) or len(result['boxes'])>128:
                    raise ValueError('bounded source-pixel detector boxes required')
                crops=[]
                for index,box in enumerate(result['boxes']):
                    if box.get('label')!='person':continue
                    threshold=self.policy['minimum_confidence']
                    if threshold is not None:
                        if not finite(box.get('confidence')) or not 0<=box['confidence']<=1:raise ValueError('detector score unavailable')
                        if box['confidence']<threshold:continue
                    coords=[box.get(k) for k in ('x','y','w','h')]
                    if not all(finite(v) for v in coords):raise ValueError('invalid person box')
                    x,y,w,h=coords
                    if not 0<=x<x+w<=width or not 0<=y<y+h<=height:raise ValueError('person outside frame')
                    crops.append({'id':'crop_'+digest([key,index])[:24],'frame_id':frame['frame_id'],
                                  'xyxy':[x,y,x+w,y+h],'detector_version':profile['model_version'],
                                  'detector_artifact_sha256':profile['artifact_sha256']})
                if len(crops)>self.policy['max_people']:raise ValueError('person count exceeds pose budget')
                bundle={**{k:frame[k] for k in ('site_id','session_id','camera_id','capture_epoch','frame_id')},
                        'crops':crops,'crop_set_digest':digest(crops),'policy':deepcopy(self.policy),
                        'reference':{k:profile[k] for k in ('profile_id','model_version','engine_epoch','artifact_sha256')},
                        'available_at':self.store.clock()}
                with self.store.transaction() as db:
                    if self.store.get_session(session['id'])['state']!='recording' or self.store.clock()>frame['valid_until']:
                        raise ValueError('shared result arrived after stop or expiry')
                    self.store.check_capacity(len(canonical(bundle))+65536)
                    event=self.store._event(db,session['id'],'perception.shared_detector',{'request_id':key,'bundle':bundle})
                    bundle['source_event_id']=event
                    db.execute("UPDATE shared_detector_stage SET state='completed',bundle=? WHERE id=?",(canonical(bundle),key))
                return bundle
            except Exception:
                with self.store.transaction() as db:
                    db.execute("UPDATE shared_detector_stage SET state='indeterminate' WHERE id=?",(key,))
                raise
